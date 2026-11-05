module q15_correlation_stream (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [7:0]   in_srcA_base,
    input  logic [2:0]   in_srcA_len_m1,
    input  logic [7:0]   in_srcB_base,
    input  logic [2:0]   in_srcB_len_m1,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [15:0]  out_data,

    output logic         sample_req_valid,
    output logic [7:0]   sample_req_addr,
    input  logic         sample_req_ready,
    input  logic         sample_resp_valid,
    input  logic [15:0]  sample_resp_rdata
);

    typedef enum logic [1:0] {IDLE, REQUEST, RESPONSE, OUTPUT} state_t;
    state_t state;
    logic [7:0] base_a, base_b;
    logic [3:0] len_a, len_b, read_index, out_index;
    logic reading_b;
    logic [127:0] a, b;

    function automatic logic [15:0] correlation(
        input logic [127:0] av, bv,
        input integer na, nb, k
    );
        logic signed [63:0] total, product, shifted;
        integer j;
        begin
            total=0;
            for (integer i=0;i<8;i=i+1) begin
                j=i-k+nb-1;
                if (i<na && j>=0 && j<nb) begin
                    product=$signed(av[16*i +: 16]) * $signed(bv[16*j +: 16]);
                    total=total+product;
                end
            end
            shifted=total >>> 15;
            if (shifted>32767) correlation=16'h7fff;
            else if (shifted < -32768) correlation=16'h8000;
            else correlation=shifted[15:0];
        end
    endfunction

    assign in_ready=(state==IDLE);
    assign out_valid=(state==OUTPUT);
    assign out_data=out_valid ? correlation(a,b,len_a,len_b,out_index) : 16'b0;
    assign sample_req_valid=(state==REQUEST);
    assign sample_req_addr=sample_req_valid ? ((reading_b ? base_b : base_a)+{4'b0,read_index}) : 8'b0;

    always_ff @(posedge clk) begin
        if (rst) begin
            state<=IDLE;
            a<=0; b<=0;
            base_a<=0; base_b<=0;
            len_a<=0; len_b<=0;
            read_index<=0; out_index<=0; reading_b<=0;
        end else begin
            case (state)
                IDLE: if (in_valid) begin
                    base_a<=in_srcA_base; base_b<=in_srcB_base;
                    len_a<={1'b0,in_srcA_len_m1}+4'd1;
                    len_b<={1'b0,in_srcB_len_m1}+4'd1;
                    read_index<=0; out_index<=0; reading_b<=0;
                    state<=REQUEST;
                end
                REQUEST: if (sample_req_ready) state<=RESPONSE;
                RESPONSE: if (sample_resp_valid) begin
                    if (reading_b) b[16*read_index +: 16]<=sample_resp_rdata;
                    else a[16*read_index +: 16]<=sample_resp_rdata;
                    if (read_index+4'd1==(reading_b ? len_b : len_a)) begin
                        read_index<=0;
                        if (reading_b) state<=OUTPUT;
                        else begin reading_b<=1; state<=REQUEST; end
                    end else begin read_index<=read_index+4'd1; state<=REQUEST; end
                end
                OUTPUT: if (out_ready) begin
                    if ({1'b0,out_index}+5'd1=={1'b0,len_a}+{1'b0,len_b}-5'd1) begin
                        out_index<=0; state<=IDLE;
                    end else out_index<=out_index+4'd1;
                end
                default: state<=IDLE;
            endcase
        end
    end
endmodule
