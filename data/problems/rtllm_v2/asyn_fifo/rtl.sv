module asyn_fifo #(parameter int WIDTH=8, parameter int DEPTH=16)(
    input logic wclk_level, rclk_level, wrstn, rrstn, winc, rinc,
    input logic [WIDTH-1:0] wdata,
    output wire wfull, rempty, output wire [WIDTH-1:0] rdata
);
    localparam int A=$clog2(DEPTH);
    logic [A:0] write_pos=0, read_pos=0;
    logic [A:0] read_first=0, read_second=0, write_first=0, write_second=0;
    logic [A:0] write_gray=0, read_gray=0;
    wire [A:0] write_next=write_pos+1'b1;
    wire [A:0] read_next=read_pos+1'b1;
    localparam logic [A:0] FULL_MASK=DEPTH | (DEPTH>>1);
    assign wfull=(write_gray==(read_second^FULL_MASK));
    assign rempty=(read_gray==write_second);
    wire push=wrstn && winc && !wfull;
    wire pop=rrstn && rinc && !rempty;
    always_ff @(posedge wclk_level or negedge wrstn) begin
        if(!wrstn) begin write_pos<=0; write_gray<=0; read_first<=0; read_second<=0; end
        else begin
            read_first<=read_gray;
            read_second<=read_first;
            if(push) begin write_pos<=write_next; write_gray<=write_next^(write_next>>1); end
        end
    end
    always_ff @(posedge rclk_level or negedge rrstn) begin
        if(!rrstn) begin read_pos<=0; read_gray<=0; write_first<=0; write_second<=0; end
        else begin
            write_first<=write_gray;
            write_second<=write_first;
            if(pop) begin read_pos<=read_next; read_gray<=read_next^(read_next>>1); end
        end
    end
    dual_port_RAM #(.WIDTH(WIDTH),.DEPTH(DEPTH)) ram(
        .wclk(wclk_level),.rclk(rclk_level),.wenc(push),.renc(pop),
        .waddr(write_pos[A-1:0]),.raddr(read_pos[A-1:0]),.wdata(wdata),.rdata(rdata));
endmodule

module dual_port_RAM #(parameter int WIDTH=8, parameter int DEPTH=16)(
    input logic wclk, rclk, wenc, renc,
    input logic [$clog2(DEPTH)-1:0] waddr, raddr,
    input logic [WIDTH-1:0] wdata, output logic [WIDTH-1:0] rdata
);
    logic [WIDTH-1:0] cells[0:DEPTH-1];
    initial begin
        rdata=0;
        for(integer i=0;i<DEPTH;i=i+1) cells[i]=0;
    end
    always_ff @(posedge wclk) if(wenc) cells[waddr]<=wdata;
    always_ff @(posedge rclk) if(renc) rdata<=cells[raddr];
endmodule
