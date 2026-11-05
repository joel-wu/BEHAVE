module spi_complex_mult #(
    parameter int IN_WIDTH=16, parameter int OUT_WIDTH=32
)(input logic rst_async_n, spi_sck_level, spi_cs_n, spi_mosi,
  output wire spi_miso);
    localparam int FRAME=4*IN_WIDTH;
    logic [2:0] byte_bits=0;
    integer byte_index=0;
    logic [7:0] partial=0;
    logic [FRAME-1:0] received=0, result=0;
    logic output_bit=0;
    wire [FRAME-1:0] assembled={received[FRAME-9:0], partial[6:0], spi_mosi};
    wire signed [IN_WIDTH-1:0] ar=assembled[FRAME-1 -: IN_WIDTH];
    wire signed [IN_WIDTH-1:0] ai=assembled[3*IN_WIDTH-1 -: IN_WIDTH];
    wire signed [IN_WIDTH-1:0] br=assembled[2*IN_WIDTH-1 -: IN_WIDTH];
    wire signed [IN_WIDTH-1:0] bi=assembled[IN_WIDTH-1:0];
    wire signed [OUT_WIDTH-1:0] real_value=ar*br-ai*bi;
    wire signed [OUT_WIDTH-1:0] imag_value=ar*bi+ai*br;
    always @(posedge spi_sck_level or posedge spi_cs_n or negedge rst_async_n) begin
        if(!rst_async_n) begin
            byte_bits<=0; byte_index<=0; partial<=0; received<=0; result<=0;
        end else if(spi_cs_n) begin
            byte_bits<=0; partial<=0;
        end else begin
            partial<={partial[6:0],spi_mosi};
            if(byte_bits==7) begin
                byte_bits<=0; partial<=0;
                if(byte_index==IN_WIDTH/2-1) begin
                    result<={real_value,imag_value}; byte_index<=0; received<=0;
                end else begin
                    received<=assembled; byte_index<=byte_index+1;
                end
            end else byte_bits<=byte_bits+1;
        end
    end
    // Mode-0 launch: transparent only in the low phase, stable when sampled high.
    always_latch begin
        if(!rst_async_n) output_bit=0;
        else if(!spi_sck_level) output_bit=result[FRAME-1-8*byte_index-byte_bits];
    end
    assign spi_miso=(rst_async_n && !spi_cs_n) ? output_bit : 1'b0;
endmodule
