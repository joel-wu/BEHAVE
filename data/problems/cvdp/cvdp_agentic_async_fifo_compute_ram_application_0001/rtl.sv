module async_fifo #(
    parameter int p_data_width=32, parameter int p_addr_width=16
)(input logic i_wr_clk_level, i_rd_clk_level, i_wr_rst_n, i_rd_rst_n,
  input logic i_wr_en, i_rd_en, input logic [p_data_width-1:0] i_wr_data,
  output logic o_fifo_full, o_fifo_empty, output logic [p_data_width-1:0] o_rd_data);
    localparam int DEPTH=1<<p_addr_width;
    localparam logic [p_addr_width:0] FULL_MASK=DEPTH | (DEPTH>>1);
    logic [p_data_width-1:0] memory[0:DEPTH-1];
    logic [p_addr_width:0] write_pos=0, read_pos=0, write_gray=0, read_gray=0;
    logic [p_addr_width:0] read_first=0, read_second=0, write_first=0, write_second=0;
    wire push=i_wr_en && !o_fifo_full;
    wire pop=i_rd_en && !o_fifo_empty;
    wire [p_addr_width:0] next_write=write_pos+push, next_read=read_pos+pop;
    wire [p_addr_width:0] next_write_gray=next_write^(next_write>>1);
    wire [p_addr_width:0] next_read_gray=next_read^(next_read>>1);
    initial begin
        o_fifo_full=0; o_fifo_empty=1; o_rd_data=0;
        for(integer i=0;i<DEPTH;i=i+1) memory[i]=0;
    end
    always @(posedge i_wr_clk_level)
        if(i_wr_rst_n && push) memory[write_pos[p_addr_width-1:0]]<=i_wr_data;
    always @(posedge i_rd_clk_level)
        if(i_rd_rst_n && pop) o_rd_data<=memory[read_pos[p_addr_width-1:0]];
    always @(posedge i_wr_clk_level or negedge i_wr_rst_n) begin
        if(!i_wr_rst_n) begin
            write_pos<=0; write_gray<=0; read_first<=0; read_second<=0; o_fifo_full<=0;
        end else begin
            read_first<=read_gray; read_second<=read_first;
            write_pos<=next_write; write_gray<=next_write_gray;
            o_fifo_full<=(next_write_gray==(read_second^FULL_MASK));
        end
    end
    always @(posedge i_rd_clk_level or negedge i_rd_rst_n) begin
        if(!i_rd_rst_n) begin
            read_pos<=0; read_gray<=0; write_first<=0; write_second<=0; o_fifo_empty<=1;
        end else begin
            write_first<=write_gray; write_second<=write_first;
            read_pos<=next_read; read_gray<=next_read_gray;
            o_fifo_empty<=(next_read_gray==write_second);
        end
    end
endmodule
