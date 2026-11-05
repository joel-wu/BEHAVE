module cache_controller (
    input logic clk, reset,
    input logic [9:0] address,
    input logic [31:0] write_data,
    input logic read, write,
    input logic [31:0] mem_read_data,
    input logic mem_ready,
    output logic [31:0] read_data,
    output logic hit, miss, mem_write,
    output logic [31:0] mem_address, mem_write_data
);
    logic [31:0] words[0:31];
    logic [4:0] tags[0:31];
    logic [31:0] valid_q;
    logic [31:0] last_data;
    logic last_hit, last_miss;
    wire line_hit=valid_q[address[4:0]] && tags[address[4:0]]==address[9:5];
    always_comb begin
        read_data=last_data; hit=last_hit; miss=last_miss;
        mem_write=0; mem_address=0; mem_write_data=0;
        if(read || write) begin
            hit=line_hit; miss=!line_hit; read_data=0;
            if(read) begin
                if(line_hit) read_data=words[address[4:0]];
                else begin
                    mem_address={22'b0,address};
                    if(mem_ready) read_data=mem_read_data;
                end
            end else begin
                mem_write=1; mem_address={22'b0,address}; mem_write_data=write_data;
            end
        end
    end
    always_ff @(posedge clk) begin
        if(reset) begin
            valid_q<=0; last_data<=0; last_hit<=0; last_miss<=0;
            for(int k=0;k<32;k++) begin words[k]<=0; tags[k]<=0; end
        end else if(read && (line_hit || mem_ready)) begin
            last_data<=read_data; last_hit<=hit; last_miss<=miss;
            if(!line_hit) begin
                words[address[4:0]]<=mem_read_data;
                tags[address[4:0]]<=address[9:5]; valid_q[address[4:0]]<=1;
            end
        end else if(write && !read && mem_ready) begin
            last_data<=0; last_hit<=hit; last_miss<=miss;
            if(line_hit) words[address[4:0]]<=write_data;
        end
    end
endmodule
