module ethernet_mii_tx (
    input logic clk_in_level, rst_in, axis_clk_in_level, axis_rst_in,
    input logic axis_valid_in,
    input logic [31:0] axis_data_in,
    input logic [3:0] axis_strb_in,
    input logic axis_last_in,
    output wire axis_ready_out,
    output logic [3:0] mii_txd_out,
    output logic mii_tx_en_out
);
    wire reset_all=rst_in | axis_rst_in;
    logic [7:0] mem[0:3071];
    logic [10:0] lengths[0:1];
    logic [1:0] write_seq=0, read_seq=0;
    wire [1:0] wg=write_seq^(write_seq>>1), rg=read_seq^(read_seq>>1);
    (* ASYNC_REG="TRUE" *) logic [1:0] rg1=0,rg2=0,wg1=0,wg2=0;
    wire [1:0] read_seen=rg2^(rg2>>1), write_seen=wg2^(wg2>>1);
    wire [1:0] full_target=read_seen+2'd2;
    logic [10:0] length=0, frame_length=0, byte_index=0;
    logic bad=0, half=0;
    logic [2:0] phase=0;
    logic [4:0] index=0, ifg=0;
    logic [31:0] crc=32'hffffffff, fcs=0;
    assign axis_ready_out=!reset_all && write_seq!=full_target;
    initial begin
        mii_txd_out=0; mii_tx_en_out=0; lengths[0]=0; lengths[1]=0;
        for(integer i=0;i<3072;i=i+1) mem[i]=0;
    end
    function automatic [31:0] update_crc(input [31:0] old_crc,input [7:0] value);
        reg [31:0] c;
        begin
            c=old_crc^value;
            for(integer k=0;k<8;k=k+1) c=(c>>1)^(c[0] ? 32'hedb88320 : 32'd0);
            update_crc=c;
        end
    endfunction
    always_ff @(posedge axis_clk_in_level or posedge reset_all) begin : receive_frame
        integer n;
        logic overflow;
        if(reset_all) begin write_seq<=0; rg1<=0; rg2<=0; length<=0; bad<=0; end
        else begin
            rg1<=rg; rg2<=rg1;
            if(axis_valid_in && axis_ready_out) begin
                n=length; overflow=bad;
                for(integer lane=0;lane<4;lane=lane+1) if(axis_strb_in[lane]) begin
                    if(n<1518) begin
                        mem[write_seq[0]*1536+n]<=axis_data_in[lane*8 +: 8]; n=n+1;
                    end else overflow=1;
                end
                length<=11'(n); bad<=overflow;
                if(axis_last_in) begin
                    lengths[write_seq[0]]<=overflow ? 11'd0 : 11'(n);
                    write_seq<=write_seq+1'b1; length<=0; bad<=0;
                end
            end
        end
    end
    always_ff @(posedge clk_in_level or posedge reset_all) begin : transmit_frame
        logic [7:0] b;
        logic [31:0] next_crc;
        if(reset_all) begin
            read_seq<=0; wg1<=0; wg2<=0; phase<=0; index<=0;
            frame_length<=0; byte_index<=0; half<=0; fcs<=0; ifg<=0;
            crc<=32'hffffffff; mii_txd_out<=0; mii_tx_en_out<=0;
        end else begin
            wg1<=wg; wg2<=wg1; mii_txd_out<=0; mii_tx_en_out<=0;
            case(phase)
                0: if(read_seq!=write_seen) begin
                    if(lengths[read_seq[0]]==0) read_seq<=read_seq+1'b1;
                    else begin
                        frame_length<=lengths[read_seq[0]]; crc<=32'hffffffff;
                        mii_txd_out<=5; mii_tx_en_out<=1; index<=1; phase<=1;
                    end
                end
                1: begin
                    mii_tx_en_out<=1;
                    if(index<15) begin mii_txd_out<=5; index<=index+1'b1; end
                    else begin mii_txd_out<=13; byte_index<=0; half<=0; phase<=2; end
                end
                2: begin
                    b=mem[read_seq[0]*1536+byte_index];
                    mii_txd_out<=half ? b[7:4] : b[3:0]; mii_tx_en_out<=1;
                    if(!half) half<=1;
                    else begin
                        half<=0; next_crc=update_crc(crc,b); crc<=next_crc;
                        if(byte_index+1==frame_length) begin fcs<=~next_crc; index<=0; phase<=3; end
                        else byte_index<=byte_index+1'b1;
                    end
                end
                3: begin
                    mii_txd_out<=fcs[index*4 +: 4]; mii_tx_en_out<=1;
                    if(index==7) begin read_seq<=read_seq+1'b1; ifg<=24; phase<=4; end
                    else index<=index+1'b1;
                end
                4: begin ifg<=ifg-1'b1; if(ifg==1) phase<=0; end
                default: phase<=0;
            endcase
        end
    end
endmodule
